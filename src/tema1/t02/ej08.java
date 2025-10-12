package tema1.t02;

import java.util.Scanner;

public class ej08 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Ingrese un numero de 3 cifras");
        int cifras = sc.nextInt();

        int centenas = cifras / 100;
        int decenas = (cifras / 10) % 10;
        int unidades = cifras % 10;

        System.out.println("centenas: " + centenas);
        System.out.println("decenas: " + decenas);
        System.out.println("unidades: " + unidades);
    }
}
