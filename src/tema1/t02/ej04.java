package tema1.t02;

import java.util.Scanner;

public class ej04 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Digite un numero entero:");
        int num = sc.nextInt();

        int doble = num * 2;
        int triple = num * 3;

        System.out.println("El doble de " + num + " es " + doble);
        System.out.println("El triple de " + num + " es " + triple);
    }
}
