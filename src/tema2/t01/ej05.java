package tema2.t01;

import java.util.Scanner;

public class ej05 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Introduce tu edad");
        int edad = sc.nextInt();

        if (edad >= 18) {
            System.out.println("Puedes votar");
        }else
        {
            System.out.println("No puedes votar");
        }
    }
}
