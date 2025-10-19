package tema1.t03;

import java.util.Scanner;

public class ej07 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Ingrese un caracter");
        char car=sc.next().charAt(0);

        System.out.println((car >= 'A' && car <= 'Z') ? "Ingresaste una letra mayuscula" : "Ingresaste una letra minuscula");
    }
}
